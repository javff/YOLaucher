--クリッター
--Sangan (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "When this card is sent from the field to the Graveyard, select 1 monster
--with an ATK of 1500 or less from your Deck, show it to your opponent, and add it to your hand. Then shuffle your Deck."
--Diferencias con CardScripts (errata moderna): (1) fuera 'You can only use this effect of "Sangan" once per turn'
--(el CountLimit(1,id): con dos Sangan caidos a la vez, como en un Dark Hole, ahora solo buscaba uno); (2) fuera 'but you
--cannot activate cards, or the effects of cards, with that name for the rest of this turn' (el bloqueo por nombre de la
--carta buscada); (3) el texto acaba con 'Then shuffle your Deck': se baraja el Deck. Lo demas igual (obligatorio, solo si
--viene del campo).
local s,id=GetID()
function s.initial_effect(c)
	--Search
	local e1=Effect.CreateEffect(c)
	e1:SetDescription(aux.Stringid(id,0))
	e1:SetCategory(CATEGORY_TOHAND+CATEGORY_SEARCH)
	e1:SetType(EFFECT_TYPE_SINGLE+EFFECT_TYPE_TRIGGER_F)
	e1:SetCode(EVENT_TO_GRAVE)
	e1:SetCondition(s.condition)
	e1:SetTarget(s.target)
	e1:SetOperation(s.operation)
	c:RegisterEffect(e1)
end
function s.condition(e,tp,eg,ep,ev,re,r,rp)
	return e:GetHandler():IsPreviousLocation(LOCATION_ONFIELD)
end
function s.target(e,tp,eg,ep,ev,re,r,rp,chk)
	if chk==0 then return true end
	Duel.SetOperationInfo(0,CATEGORY_TOHAND,nil,1,tp,LOCATION_DECK)
end
function s.filter(c)
	return c:IsAttackBelow(1500) and c:IsMonster() and c:IsAbleToHand()
end
function s.operation(e,tp,eg,ep,ev,re,r,rp)
	Duel.Hint(HINT_SELECTMSG,tp,HINTMSG_ATOHAND)
	local g=Duel.SelectMatchingCard(tp,s.filter,tp,LOCATION_DECK,0,1,1,nil)
	if #g>0 then
		Duel.SendtoHand(g,nil,REASON_EFFECT)
		Duel.ConfirmCards(1-tp,g)
		--"Then shuffle your Deck"
		Duel.ShuffleDeck(tp)
	end
end

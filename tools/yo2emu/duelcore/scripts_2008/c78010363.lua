--黒き森のウィッチ
--Witch of the Black Forest (texto de 2008)
--Texto del cliente 2008 (merge.dat, card_desc_e): "When this card is sent from the field to the Graveyard, select 1 monster
--with a DEF of 1500 or less from your Deck, show it to your opponent, and add it to your hand. Then shuffle your Deck."
--Diferencias con CardScripts (errata moderna): las mismas que Sangan (c26202165.lua): sin 'once per turn' (CountLimit),
--sin el bloqueo por nombre de la carta buscada, y se baraja el Deck al final.
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
	return c:IsDefenseBelow(1500) and c:IsMonster() and c:IsAbleToHand()
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
